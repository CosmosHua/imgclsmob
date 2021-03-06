import argparse
import logging
import os
import re
import numpy as np

import mxnet as mx
import torch

from common.logger_utils import initialize_logging


def parse_args():
    parser = argparse.ArgumentParser(description='Convert models (Gluon/PyTorch/Chainer/MXNet)',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--src-fwk',
        type=str,
        required=True,
        help='source model framework name')
    parser.add_argument(
        '--dst-fwk',
        type=str,
        required=True,
        help='destination model framework name')
    parser.add_argument(
        '--src-model',
        type=str,
        required=True,
        help='source model name')
    parser.add_argument(
        '--dst-model',
        type=str,
        required=True,
        help='destination model name')
    parser.add_argument(
        '--src-params',
        type=str,
        default='',
        help='source model parameter file path')
    parser.add_argument(
        '--dst-params',
        type=str,
        default='',
        help='destination model parameter file path')

    parser.add_argument(
        '--save-dir',
        type=str,
        default='',
        help='directory of saved models and log-files')
    parser.add_argument(
        '--logging-file-name',
        type=str,
        default='train.log',
        help='filename of training log')
    args = parser.parse_args()
    return args


def prepare_model_gl(model_name,
                     classes,
                     use_pretrained,
                     pretrained_model_file_path,
                     batch_norm,
                     use_se,
                     last_gamma,
                     dtype,
                     ctx):
    kwargs = {'ctx': ctx,
              'pretrained': use_pretrained,
              'classes': classes}

    if model_name.startswith('vgg'):
        kwargs['batch_norm'] = batch_norm
    elif model_name.startswith('resnext'):
        kwargs['use_se'] = use_se

    if last_gamma:
        kwargs['last_gamma'] = True

    from gluon.model_utils import get_model
    net = get_model(model_name, **kwargs)

    if pretrained_model_file_path:
        assert (os.path.isfile(pretrained_model_file_path))
        logging.info('Loading model: {}'.format(pretrained_model_file_path))
        net.load_parameters(
            filename=pretrained_model_file_path,
            ctx=ctx)

    net.cast(dtype)

    net.hybridize(
        static_alloc=True,
        static_shape=True)

    if pretrained_model_file_path or use_pretrained:
        for param in net.collect_params().values():
            if param._data is not None:
                continue
            param.initialize(mx.init.MSRAPrelu(), ctx=ctx)
    else:
        net.initialize(mx.init.MSRAPrelu(), ctx=ctx)

    return net


def prepare_model_pt(model_name,
                     classes,
                     use_pretrained,
                     pretrained_model_file_path,
                     use_cuda):
    kwargs = {'pretrained': use_pretrained,
              'num_classes': classes}

    from pytorch.model_utils import get_model
    net = get_model(model_name, **kwargs)

    if pretrained_model_file_path:
        assert (os.path.isfile(pretrained_model_file_path))
        logging.info('Loading model: {}'.format(pretrained_model_file_path))
        checkpoint = torch.load(
            pretrained_model_file_path,
            map_location=(None if use_cuda else 'cpu'))
        if type(checkpoint) == dict:
            net.load_state_dict(checkpoint['state_dict'])
        else:
            net.load_state_dict(checkpoint)

    if use_cuda:
        net = net.cuda()

    return net


def prepare_model_ch(model_name,
                     classes,
                     use_pretrained,
                     pretrained_model_file_path):
    kwargs = {'pretrained': use_pretrained,
              'classes': classes}

    from chainer_.model_utils import get_model
    net = get_model(model_name, **kwargs)

    if pretrained_model_file_path:
        assert (os.path.isfile(pretrained_model_file_path))
        logging.info('Loading model: {}'.format(pretrained_model_file_path))
        from chainer.serializers import load_npz
        load_npz(
            file=pretrained_model_file_path,
            obj=net)

    return net


def prepare_model_mx(pretrained_model_file_path,
                     ctx):

    # net = mx.mod.Module.load(
    #     prefix=pretrained_model_file_path,
    #     epoch=0,
    #     context=[ctx],
    #     label_names=[])
    # net.bind(
    #     data_shapes=[('data', (1, 3, 224, 224))],
    #     label_shapes=None,
    #     for_training=False)

    sym, arg_params, aux_params = mx.model.load_checkpoint(pretrained_model_file_path, 0)

    return sym, arg_params, aux_params


def prepare_src_model(src_fwk,
                      src_model,
                      src_params_file_path,
                      dst_fwk,
                      num_classes,
                      ctx,
                      use_cuda):

    ext_src_param_keys = None
    ext_src_param_keys2 = None
    src_arg_params = None

    if src_fwk == "gluon":
        src_net = prepare_model_gl(
            model_name=src_model,
            classes=num_classes,
            use_pretrained=False,
            pretrained_model_file_path=src_params_file_path,
            batch_norm=False,
            use_se=False,
            last_gamma=False,
            dtype=np.float32,
            ctx=ctx)
        src_params = src_net._collect_params_with_prefix()
        src_param_keys = list(src_params.keys())

        if src_model in ["resnet50_v1", "resnet101_v1", "resnet152_v1"]:
            src_param_keys = [key for key in src_param_keys if
                              not (key.startswith("features.") and key.endswith(".bias"))]

        if src_model in ["resnet18_v2", "resnet34_v2", "resnet50_v2", "resnet101_v2", "resnet152_v2"]:
            src_param_keys = src_param_keys[4:]

        if dst_fwk == "chainer":
            src_param_keys_ = src_param_keys.copy()
            src_param_keys = [key for key in src_param_keys_ if (not key.endswith(".running_mean")) and
                              (not key.endswith(".running_var"))]
            ext_src_param_keys = [key for key in src_param_keys_ if (key.endswith(".running_mean")) or
                                  (key.endswith(".running_var"))]
            if src_model in ["condensenet74_c4_g4", "condensenet74_c8_g8"]:
                src_param_keys_ = src_param_keys.copy()
                src_param_keys = [key for key in src_param_keys_ if (not key.endswith(".index"))]
                ext_src_param_keys2 = [key for key in src_param_keys_ if (key.endswith(".index"))]

    elif src_fwk == "pytorch":
        src_net = prepare_model_pt(
            model_name=src_model,
            classes=num_classes,
            use_pretrained=False,
            pretrained_model_file_path=src_params_file_path,
            use_cuda=use_cuda)
        src_params = src_net.state_dict()
        src_param_keys = list(src_params.keys())
        if dst_fwk != "pytorch":
            src_param_keys = [key for key in src_param_keys if not key.endswith("num_batches_tracked")]
        if src_model in ["oth_shufflenetv2_wd2"]:
            src_param_keys = [key for key in src_param_keys if
                              not key.startswith("network.0.")]

    elif src_fwk == "mxnet":
        src_sym, src_arg_params, src_aux_params = prepare_model_mx(
            pretrained_model_file_path=src_params_file_path,
            ctx=ctx)
        src_params = None
        src_param_keys = list(src_arg_params.keys())
    else:
        raise ValueError("Unsupported src fwk: {}".format(src_fwk))

    return src_params, src_param_keys, ext_src_param_keys, ext_src_param_keys2, src_arg_params


def prepare_dst_model(dst_fwk,
                      dst_model,
                      src_fwk,
                      num_classes,
                      ctx,
                      use_cuda):

    if dst_fwk == "gluon":
        dst_net = prepare_model_gl(
            model_name=dst_model,
            classes=num_classes,
            use_pretrained=False,
            pretrained_model_file_path="",
            batch_norm=False,
            use_se=False,
            last_gamma=False,
            dtype=np.float32,
            ctx=ctx)
        dst_params = dst_net._collect_params_with_prefix()
        dst_param_keys = list(dst_params.keys())
    elif dst_fwk == "pytorch":
        dst_net = prepare_model_pt(
            model_name=dst_model,
            classes=num_classes,
            use_pretrained=False,
            pretrained_model_file_path="",
            use_cuda=use_cuda)
        dst_params = dst_net.state_dict()
        dst_param_keys = list(dst_params.keys())
        if src_fwk != "pytorch":
            dst_param_keys = [key for key in dst_param_keys if not key.endswith("num_batches_tracked")]
    elif dst_fwk == "chainer":
        dst_net = prepare_model_ch(
            model_name=dst_model,
            classes=num_classes,
            use_pretrained=False,
            pretrained_model_file_path="")
        dst_params = {i[0]: i[1] for i in dst_net.namedparams()}
        dst_param_keys = list(dst_params.keys())
    else:
        raise ValueError("Unsupported dst fwk: {}".format(dst_fwk))

    return dst_params, dst_param_keys, dst_net


def convert_mx2gl(dst_net,
                  dst_params_file_path,
                  dst_params,
                  dst_param_keys,
                  src_param_keys,
                  src_arg_params,
                  src_model,
                  ctx):
    if src_model in ["preresnet200b"]:
        src_param_keys = [key for key in src_param_keys if (key.startswith("stage"))]

        dst_params['features.0.conv.weight']._load_init(src_arg_params['conv0_weight'], ctx)
        dst_params['features.0.bn.beta']._load_init(src_arg_params['bn0_beta'], ctx)
        dst_params['features.0.bn.gamma']._load_init(src_arg_params['bn0_gamma'], ctx)
        dst_params['features.5.bn.beta']._load_init(src_arg_params['bn1_beta'], ctx)
        dst_params['features.5.bn.gamma']._load_init(src_arg_params['bn1_gamma'], ctx)
        dst_params['output.1.bias']._load_init(src_arg_params['fc1_bias'], ctx)
        dst_params['output.1.weight']._load_init(src_arg_params['fc1_weight'], ctx)

        dst_param_keys = [key for key in dst_param_keys if (not key.endswith("running_mean") and
                                                            not key.endswith("running_var") and
                                                            key.startswith("features") and
                                                            not key.startswith("features.0") and
                                                            not key.startswith("features.5"))]

        # src_param_keys = [key.replace('stage', 'features.') for key in src_param_keys]
        # src_param_keys = [key.replace('_', '.') for key in src_param_keys]

        src_param_keys = [key.replace('_conv1_', '.conv1.conv.') for key in src_param_keys]
        src_param_keys = [key.replace('_conv2_', '.conv2.conv.') for key in src_param_keys]
        src_param_keys = [key.replace('_conv3_', '.conv3.conv.') for key in src_param_keys]
        src_param_keys = [key.replace('_bn1_', '.conv1.bn.') for key in src_param_keys]
        src_param_keys = [key.replace('_bn2_', '.conv2.bn.') for key in src_param_keys]
        src_param_keys = [key.replace('_bn3_', '.conv3.bn.') for key in src_param_keys]

        src_param_keys.sort()
        src_param_keys.sort(key=lambda var: ['{:10}'.format(int(x)) if
                                             x.isdigit() else x for x in re.findall(r'[^0-9]|[0-9]+', var)])

        dst_param_keys.sort()
        dst_param_keys.sort(key=lambda var: ['{:10}'.format(int(x)) if
                                             x.isdigit() else x for x in re.findall(r'[^0-9]|[0-9]+', var)])

        src_param_keys = [key.replace('.conv1.conv.', '_conv1_') for key in src_param_keys]
        src_param_keys = [key.replace('.conv2.conv.', '_conv2_') for key in src_param_keys]
        src_param_keys = [key.replace('.conv3.conv.', '_conv3_') for key in src_param_keys]
        src_param_keys = [key.replace('.conv1.bn.', '_bn1_') for key in src_param_keys]
        src_param_keys = [key.replace('.conv2.bn.', '_bn2_') for key in src_param_keys]
        src_param_keys = [key.replace('.conv3.bn.', '_bn3_') for key in src_param_keys]

    for i, (src_key, dst_key) in enumerate(zip(src_param_keys, dst_param_keys)):
        dst_params[dst_key]._load_init(src_arg_params[src_key], ctx)

    for param in dst_net.collect_params().values():
        if param._data is not None:
            continue
        print('param={}'.format(param))
        param.initialize(ctx=ctx)

    dst_net.save_parameters(dst_params_file_path)


def convert_gl2ch(dst_net,
                  dst_params_file_path,
                  dst_params,
                  dst_param_keys,
                  src_params,
                  src_param_keys,
                  ext_src_param_keys,
                  ext_src_param_keys2,
                  src_model):

    dst_param_keys = [key.replace('/W', '/weight') for key in dst_param_keys]
    dst_param_keys = [key.replace('/post_activ/', '/stageN/post_activ/') for key in dst_param_keys]
    dst_param_keys = [key.replace('/final_block/', '/stageN/final_block/') for key in dst_param_keys]
    dst_param_keys = [key.replace('/stem1_unit/', '/stage0/stem1_unit/') for key in dst_param_keys]
    dst_param_keys = [key.replace('/stem2_unit/', '/stage0/stem2_unit/') for key in dst_param_keys]

    src_param_keys.sort()
    src_param_keys.sort(key=lambda var: ['{:10}'.format(int(x)) if
                                         x.isdigit() else x for x in re.findall(r'[^0-9]|[0-9]+', var)])

    dst_param_keys.sort()
    dst_param_keys.sort(key=lambda var: ['{:10}'.format(int(x)) if
                                         x.isdigit() else x for x in re.findall(r'[^0-9]|[0-9]+', var)])

    dst_param_keys = [key.replace('/weight', '/W') for key in dst_param_keys]
    dst_param_keys = [key.replace('/stageN/post_activ/', '/post_activ/') for key in dst_param_keys]
    dst_param_keys = [key.replace('/stageN/final_block/', '/final_block/') for key in dst_param_keys]
    dst_param_keys = [key.replace('/stage0/stem1_unit/', '/stem1_unit/') for key in dst_param_keys]
    dst_param_keys = [key.replace('/stage0/stem2_unit/', '/stem2_unit/') for key in dst_param_keys]

    ext2_src_param_keys = [key for key in src_param_keys if key.endswith(".beta")]
    ext2_dst_param_keys = [key for key in dst_param_keys if key.endswith("/beta")]
    ext3_src_param_keys = {".".join(v.split(".")[:-1]): i for i, v in enumerate(ext2_src_param_keys)}
    ext3_dst_param_keys = list(map(lambda x: x.split('/')[1:-1], ext2_dst_param_keys))

    for i, src_key in enumerate(ext_src_param_keys):
        src_key1 = src_key.split(".")[-1]
        src_key2 = ".".join(src_key.split(".")[:-1])
        dst_ind = ext3_src_param_keys[src_key2]
        dst_path = ext3_dst_param_keys[dst_ind]
        obj = dst_net
        for j, sub_path in enumerate(dst_path):
            obj = getattr(obj, sub_path)
        if src_key1 == 'running_mean':
            assert (obj.avg_mean.shape == src_params[src_key].shape), \
                "src_key={}, dst_path={}, src_shape={}, obj.avg_mean.shape={}".format(
                    src_key, dst_path, src_params[src_key].shape, obj.avg_mean.shape)
            obj.avg_mean = src_params[src_key]._data[0].asnumpy()
        elif src_key1 == 'running_var':
            assert (obj.avg_var.shape == src_params[src_key].shape)
            obj.avg_var = src_params[src_key]._data[0].asnumpy()

    if src_model in ["condensenet74_c4_g4", "condensenet74_c8_g8"]:
        assert (dst_net.output.fc.index.shape == src_params["output.1.index"].shape)
        dst_net.output.fc.index = src_params["output.1.index"]._data[0].asnumpy().astype(np.int32)
        ext_src_param_keys2.remove("output.1.index")

        ext2_src_param_keys = [key for key in src_param_keys if key.endswith(".conv1.conv.weight")]
        ext2_dst_param_keys = [key for key in dst_param_keys if key.endswith("/conv1/conv/W")]
        ext3_src_param_keys = {".".join(v.split(".")[:-2]): i for i, v in enumerate(ext2_src_param_keys)}
        ext3_dst_param_keys = list(map(lambda x: x.split('/')[1:-2], ext2_dst_param_keys))

        for i, src_key in enumerate(ext_src_param_keys2):
            src_key2 = ".".join(src_key.split(".")[:-1])
            dst_ind = ext3_src_param_keys[src_key2]
            dst_path = ext3_dst_param_keys[dst_ind]
            obj = dst_net
            for j, sub_path in enumerate(dst_path):
                obj = getattr(obj, sub_path)
            assert (obj.index.shape == src_params[src_key].shape), \
                "src_key={}, dst_path={}, src_shape={}, obj.index.shape={}".format(
                    src_key, dst_path, src_params[src_key].shape, obj.index.shape)
            obj.index = src_params[src_key]._data[0].asnumpy().astype(np.int32)

    for i, (src_key, dst_key) in enumerate(zip(src_param_keys, dst_param_keys)):
        assert (dst_params[dst_key].array.shape == src_params[src_key].shape), \
            "src_key={}, dst_key={}, src_shape={}, dst_shape={}".format(
                src_key, dst_key, src_params[src_key].shape, dst_params[dst_key].array.shape)
        dst_params[dst_key].array = src_params[src_key]._data[0].asnumpy()

    from chainer.serializers import save_npz
    save_npz(
        file=dst_params_file_path,
        obj=dst_net)


def convert_gl2gl(dst_net,
                  dst_params_file_path,
                  dst_params,
                  dst_param_keys,
                  src_params,
                  src_param_keys,
                  ctx):
    for i, (src_key, dst_key) in enumerate(zip(src_param_keys, dst_param_keys)):
        assert (dst_params[dst_key].shape == src_params[src_key].shape)
        assert (dst_key.split('.')[-1] == src_key.split('.')[-1])
        dst_params[dst_key]._load_init(src_params[src_key]._data[0], ctx)
    dst_net.save_parameters(dst_params_file_path)


def convert_pt2pt(dst_params_file_path,
                  dst_params,
                  dst_param_keys,
                  src_params,
                  src_param_keys,
                  src_model,
                  dst_model):
    for i, (src_key, dst_key) in enumerate(zip(src_param_keys, dst_param_keys)):
        if (src_model == "oth_shufflenetv2_wd2" and dst_model == "shufflenetv2_wd2") and \
                (src_key == "network.8.weight"):
            dst_params[dst_key] = torch.from_numpy(src_params[src_key].numpy()[:, :, 0, 0])
        else:
            assert (tuple(dst_params[dst_key].size()) == tuple(src_params[src_key].size())), \
                "src_key={}, dst_key={}, src_shape={}, dst_shape={}".format(
                    src_key, dst_key, tuple(src_params[src_key].size()), tuple(dst_params[dst_key].size()))
            assert (dst_key.split('.')[-1] == src_key.split('.')[-1])
            dst_params[dst_key] = torch.from_numpy(src_params[src_key].numpy())
    torch.save(
        obj=dst_params,
        f=dst_params_file_path)


def convert_gl2pt(dst_params_file_path,
                  dst_params,
                  dst_param_keys,
                  src_params,
                  src_param_keys):
    for i, (src_key, dst_key) in enumerate(zip(src_param_keys, dst_param_keys)):
        assert (tuple(dst_params[dst_key].size()) == src_params[src_key].shape)
        dst_params[dst_key] = torch.from_numpy(src_params[src_key]._data[0].asnumpy())
    torch.save(
        obj=dst_params,
        f=dst_params_file_path)


def convert_pt2gl(dst_net,
                  dst_params_file_path,
                  dst_params,
                  dst_param_keys,
                  src_params,
                  src_param_keys,
                  ctx):
    for i, (src_key, dst_key) in enumerate(zip(src_param_keys, dst_param_keys)):
        assert (dst_params[dst_key].shape == tuple(src_params[src_key].size())), \
            "src_key={}, dst_key={}, src_shape={}, dst_shape={}".format(
                src_key, dst_key, tuple(src_params[src_key].size()), dst_params[dst_key].shape)
        dst_params[dst_key]._load_init(mx.nd.array(src_params[src_key].numpy(), ctx), ctx)
    dst_net.save_parameters(dst_params_file_path)


def main():
    args = parse_args()

    packages = []
    pip_packages = []
    if (args.src_fwk == "gluon") or (args.dst_fwk == "gluon"):
        packages += ['mxnet']
        pip_packages += ['mxnet-cu92', 'gluoncv']
    if (args.src_fwk == "pytorch") or (args.dst_fwk == "pytorch"):
        packages += ['torch', 'torchvision']
    if (args.src_fwk == "chainer") or (args.dst_fwk == "chainer"):
        packages += ['chainer', 'chainercv']
        pip_packages += ['cupy-cuda92', 'chainer', 'chainercv']

    _, log_file_exist = initialize_logging(
        logging_dir_path=args.save_dir,
        logging_file_name=args.logging_file_name,
        script_args=args,
        log_packages=packages,
        log_pip_packages=pip_packages)

    ctx = mx.cpu()
    use_cuda = False
    num_classes = 1000

    src_params, src_param_keys, ext_src_param_keys, ext_src_param_keys2, src_arg_params = prepare_src_model(
        src_fwk=args.src_fwk,
        src_model=args.src_model,
        src_params_file_path=args.src_params,
        dst_fwk=args.dst_fwk,
        num_classes=num_classes,
        ctx=ctx,
        use_cuda=use_cuda)

    dst_params, dst_param_keys, dst_net = prepare_dst_model(
        dst_fwk=args.dst_fwk,
        dst_model=args.dst_model,
        src_fwk=args.src_fwk,
        num_classes=num_classes,
        ctx=ctx,
        use_cuda=use_cuda)

    if (args.src_fwk == "mxnet") and (args.src_model in ["preresnet200b"]):
        assert (len(src_param_keys) <= len(dst_param_keys))
    else:
        assert (len(src_param_keys) == len(dst_param_keys))

    if args.src_fwk == "gluon" and args.dst_fwk == "gluon":
        convert_gl2gl(
            dst_net=dst_net,
            dst_params_file_path=args.dst_params,
            dst_params=dst_params,
            dst_param_keys=dst_param_keys,
            src_params=src_params,
            src_param_keys=src_param_keys,
            ctx=ctx)
    elif args.src_fwk == "pytorch" and args.dst_fwk == "pytorch":
        convert_pt2pt(
            dst_params_file_path=args.dst_params,
            dst_params=dst_params,
            dst_param_keys=dst_param_keys,
            src_params=src_params,
            src_param_keys=src_param_keys,
            src_model=args.src_model,
            dst_model=args.dst_model)
    elif args.src_fwk == "gluon" and args.dst_fwk == "pytorch":
        convert_gl2pt(
            dst_params_file_path=args.dst_params,
            dst_params=dst_params,
            dst_param_keys=dst_param_keys,
            src_params=src_params,
            src_param_keys=src_param_keys)
    elif args.src_fwk == "gluon" and args.dst_fwk == "chainer":
        convert_gl2ch(
            dst_net=dst_net,
            dst_params_file_path=args.dst_params,
            dst_params=dst_params,
            dst_param_keys=dst_param_keys,
            src_params=src_params,
            src_param_keys=src_param_keys,
            ext_src_param_keys=ext_src_param_keys,
            ext_src_param_keys2=ext_src_param_keys2,
            src_model=args.src_model)

    elif args.src_fwk == "pytorch" and args.dst_fwk == "gluon":
        convert_pt2gl(
            dst_net=dst_net,
            dst_params_file_path=args.dst_params,
            dst_params=dst_params,
            dst_param_keys=dst_param_keys,
            src_params=src_params,
            src_param_keys=src_param_keys,
            ctx=ctx)
    elif args.src_fwk == "mxnet" and args.dst_fwk == "gluon":
        convert_mx2gl(
            dst_net=dst_net,
            dst_params_file_path=args.dst_params,
            dst_params=dst_params,
            dst_param_keys=dst_param_keys,
            src_param_keys=src_param_keys,
            src_arg_params=src_arg_params,
            src_model=args.src_model,
            ctx=ctx)

    logging.info('Convert {}-model {} into {}-model {}'.format(
        args.src_fwk, args.src_model, args.dst_fwk, args.dst_model))


if __name__ == '__main__':
    main()
